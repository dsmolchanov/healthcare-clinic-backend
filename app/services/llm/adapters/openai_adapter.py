import os
import json
import time
from typing import Dict, List, Any, Optional, AsyncIterator
from openai import AsyncOpenAI
from app.services.llm.base_adapter import LLMAdapter, LLMResponse, ToolCall, ModelCapability
import logging

logger = logging.getLogger(__name__)


class OpenAIAdapter(LLMAdapter):
    """Adapter for OpenAI models (fallback provider)"""

    def __init__(self, capability: ModelCapability):
        super().__init__(capability)

        api_key = os.environ.get(capability.requires_api_key_env_var)
        if not api_key:
            raise ValueError(f"Missing API key: {capability.requires_api_key_env_var}")

        self.client = AsyncOpenAI(api_key=api_key)

    async def generate(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> LLMResponse:
        """Generate response without tools"""
        start_time = time.time()

        params = self.sanitize_parameters(kwargs)

        # GPT-5-nano only supports temperature=1 (default)
        # Remove temperature param to use default
        api_params = {
            "model": self.model,
            "messages": messages,
            "max_completion_tokens": max_tokens or self.capability.max_output_tokens,
            **params
        }

        # Only add temperature if not gpt-5-nano
        if 'gpt-5-nano' not in self.model.lower():
            api_params["temperature"] = temperature

        try:
            response = await self.client.chat.completions.create(**api_params)

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
                latency_ms=latency_ms
            )

        except Exception as e:
            logger.error(f"OpenAI generation error: {e}")
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

        params = self.sanitize_parameters(kwargs)

        # GPT-5-nano only supports temperature=1 (default)
        # Remove temperature param to use default
        # Get tool_choice - default to 'auto' but allow override
        tool_choice = params.pop('tool_choice', 'auto')

        api_params = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": params.pop('parallel_tool_calls', True),
            "max_completion_tokens": max_tokens or self.capability.max_output_tokens,
            **params
        }

        # Only add temperature if not gpt-5-nano
        if 'gpt-5-nano' not in self.model.lower():
            api_params["temperature"] = temperature

        try:
            response = await self.client.chat.completions.create(**api_params)

            latency_ms = int((time.time() - start_time) * 1000)

            # Normalize tool calls
            tool_calls = self.normalize_tool_calls(response)

            return LLMResponse(
                content=response.choices[0].message.content,
                tool_calls=tool_calls,
                provider=self.provider,
                model=self.model,
                usage={
                    'input_tokens': response.usage.prompt_tokens,
                    'output_tokens': response.usage.completion_tokens,
                    'total_tokens': response.usage.total_tokens
                },
                latency_ms=latency_ms
            )

        except Exception as e:
            logger.error(f"OpenAI tool calling error: {e}")
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
            max_completion_tokens=max_tokens or self.capability.max_output_tokens,
            stream=True,
            **params
        )

        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

    def sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Remove unsupported parameters for OpenAI"""
        allowed = {
            'top_p', 'presence_penalty', 'frequency_penalty',
            'stop', 'user', 'tool_choice', 'parallel_tool_calls'
        }
        return {k: v for k, v in params.items() if k in allowed}

    def normalize_tool_calls(self, response: Any) -> List[ToolCall]:
        """Normalize OpenAI tool calls (already in standard format)"""
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
