import os
import json
import time
from typing import Dict, List, Any, Optional, AsyncIterator
from google import genai
from google.genai import types
from app.services.llm.base_adapter import LLMAdapter, LLMResponse, ToolCall, ModelCapability
import logging

logger = logging.getLogger(__name__)


class GeminiAdapter(LLMAdapter):
    """Adapter for Google Gemini models"""

    def __init__(self, capability: ModelCapability):
        super().__init__(capability)

        api_key = os.environ.get(capability.requires_api_key_env_var)
        if not api_key:
            raise ValueError(f"Missing API key: {capability.requires_api_key_env_var}")

        # Use GEMINI_API_KEY or GOOGLE_API_KEY
        os.environ['GEMINI_API_KEY'] = api_key
        self.client = genai.Client(api_key=api_key)

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

        # Convert messages to Gemini format
        gemini_messages = self._convert_messages(messages)

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=gemini_messages,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens or self.capability.max_output_tokens,
                    **params
                )
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # Handle safety blocks
            if response.candidates[0].finish_reason == 'SAFETY':
                logger.warning(f"Gemini response blocked by safety filters")
                raise ValueError("Response blocked by safety filters")

            return LLMResponse(
                content=response.text,
                tool_calls=[],
                provider=self.provider,
                model=self.model,
                usage={
                    'input_tokens': response.usage_metadata.prompt_token_count,
                    'output_tokens': response.usage_metadata.candidates_token_count,
                    'total_tokens': response.usage_metadata.total_token_count
                },
                latency_ms=latency_ms
            )

        except Exception as e:
            logger.error(f"Gemini generation error: {e}")
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
        gemini_messages = self._convert_messages(messages)
        gemini_tools = self._convert_tools(tools)

        try:
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=gemini_messages,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens or self.capability.max_output_tokens,
                    tools=gemini_tools,  # Tools should be in config, not separate parameter
                    **params
                )
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # Normalize tool calls
            tool_calls = self.normalize_tool_calls(response)

            return LLMResponse(
                content=response.text if response.text else None,
                tool_calls=tool_calls,
                provider=self.provider,
                model=self.model,
                usage={
                    'input_tokens': response.usage_metadata.prompt_token_count,
                    'output_tokens': response.usage_metadata.candidates_token_count,
                    'total_tokens': response.usage_metadata.total_token_count
                },
                latency_ms=latency_ms
            )

        except Exception as e:
            logger.error(f"Gemini tool calling error: {e}")
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
        gemini_messages = self._convert_messages(messages)

        async for chunk in self.client.aio.models.generate_content_stream(
            model=self.model,
            contents=gemini_messages,
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens or self.capability.max_output_tokens,
                **params
            )
        ):
            if chunk.text:
                yield chunk.text

    def sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Remove unsupported parameters for Gemini"""
        # Gemini supports: top_p, top_k
        allowed = {'top_p', 'top_k'}
        return {k: v for k, v in params.items() if k in allowed}

    def normalize_tool_calls(self, response: Any) -> List[ToolCall]:
        """Normalize Gemini function calls to common format"""
        if not response.candidates[0].content.parts:
            return []

        normalized = []
        for part in response.candidates[0].content.parts:
            if hasattr(part, 'function_call') and part.function_call:
                fc = part.function_call
                normalized.append(ToolCall(
                    id=f"gemini_{fc.name}_{int(time.time() * 1000)}",  # Generate ID
                    name=fc.name,
                    arguments=dict(fc.args)
                ))

        return normalized

    def _convert_messages(self, messages: List[Dict[str, str]]) -> str:
        """Convert OpenAI message format to Gemini format"""
        # Simple conversion: join user messages
        # For more complex conversion, handle system/user/assistant separately
        user_messages = [m['content'] for m in messages if m['role'] == 'user']
        return '\n'.join(user_messages)

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[types.Tool]:
        """Convert OpenAI tool format to Gemini Tool format"""
        from google.genai.types import FunctionDeclaration, Tool

        function_declarations = []
        for tool in tools:
            if tool['type'] == 'function':
                func = tool['function']

                # Convert parameters schema
                parameters = func.get('parameters', {})

                # Create FunctionDeclaration
                function_declarations.append(
                    FunctionDeclaration(
                        name=func['name'],
                        description=func.get('description', ''),
                        parameters=parameters
                    )
                )

        # Wrap in Tool object
        return [Tool(function_declarations=function_declarations)] if function_declarations else []
