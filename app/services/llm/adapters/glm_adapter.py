import os
import json
import time
from typing import Dict, List, Any, Optional, AsyncIterator
from openai import AsyncOpenAI  # GLM is OpenAI-compatible
from app.services.llm.base_adapter import LLMAdapter, LLMResponse, ToolCall, ModelCapability
import logging

logger = logging.getLogger(__name__)


class GLMAdapter(LLMAdapter):
    """Adapter for Zhipu AI GLM models (OpenAI-compatible API)"""

    def __init__(self, capability: ModelCapability):
        super().__init__(capability)

        api_key = os.environ.get(capability.requires_api_key_env_var)
        if not api_key:
            raise ValueError(f"Missing API key: {capability.requires_api_key_env_var}")

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=capability.base_url_override or "https://api.z.ai/api/paas/v4"
        )

        self.supports_thinking = capability.supports_thinking_mode

    async def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> LLMResponse:
        """Generate response without tools"""
        start_time = time.time()
        ttft = None

        params = self.sanitize_parameters(kwargs)

        # Add thinking mode if supported and requested
        if self.supports_thinking and params.pop('enable_thinking', False):
            params['thinking'] = {'type': 'enabled'}

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens or self.capability.max_output_tokens,
                **params
            )

            latency_ms = int((time.time() - start_time) * 1000)

            return LLMResponse(
                content=response.choices[0].message.content,
                tool_calls=[],
                provider=self.provider,
                model=self.model,
                usage={
                    'input_tokens': response.usage.prompt_tokens,
                    'output_tokens': response.usage.completion_tokens,
                    'total_tokens': response.usage.total_tokens
                },
                latency_ms=latency_ms,
                ttft_ms=ttft
            )

        except Exception as e:
            logger.error(f"GLM generation error: {e}")
            raise

    async def generate_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> LLMResponse:
        """Generate response with tool calling"""
        start_time = time.time()
        ttft = None

        params = self.sanitize_parameters(kwargs)

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,  # OpenAI-compatible format
                tool_choice=params.pop('tool_choice', 'auto'),
                temperature=temperature,
                max_tokens=max_tokens or self.capability.max_output_tokens,
                **params
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # Normalize tool calls
            tool_calls = self.normalize_tool_calls(response)

            # Extract content (may be None for thinking-heavy responses)
            content = response.choices[0].message.content or ""

            # Log raw response for debugging
            logger.info(f"GLM raw response content (length: {len(content)}): {content[:200] if content else '(empty)'}")

            return LLMResponse(
                content=content,
                tool_calls=tool_calls,
                provider=self.provider,
                model=self.model,
                usage={
                    'input_tokens': response.usage.prompt_tokens,
                    'output_tokens': response.usage.completion_tokens,
                    'total_tokens': response.usage.total_tokens
                },
                latency_ms=latency_ms,
                ttft_ms=ttft
            )

        except Exception as e:
            logger.error(f"GLM tool calling error: {e}")
            raise

    async def stream(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """Stream response chunks"""
        params = self.sanitize_parameters(kwargs)

        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens or self.capability.max_output_tokens,
            stream=True,
            **params
        )

        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Remove unsupported parameters for GLM"""
        # GLM supports: top_p, presence_penalty, frequency_penalty, stop, user
        allowed = {
            'top_p', 'presence_penalty', 'frequency_penalty',
            'stop', 'user', 'enable_thinking', 'tool_choice'
        }
        return {k: v for k, v in params.items() if k in allowed}

    def normalize_tool_calls(self, response: Any) -> List[ToolCall]:
        """Normalize GLM tool calls to common format"""
        if not response.choices[0].message.tool_calls:
            return []

        normalized = []
        for tc in response.choices[0].message.tool_calls:
            normalized.append(ToolCall(
                id=tc.id,
                name=tc.function.name,
                arguments=json.loads(tc.function.arguments)
            ))

        return normalized
