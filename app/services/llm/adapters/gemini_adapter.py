import os
import json
import time
from typing import Dict, List, Any, Optional, AsyncIterator, Tuple
from google import genai
from google.genai import types
from google.genai.types import Content, Part, FunctionCall, FunctionResponse
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

        # Convert messages and extract system instruction
        system_instruction, gemini_contents = self._convert_messages(messages)

        try:
            config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens or self.capability.max_output_tokens,
                **params
            )

            # Add system instruction if present
            if system_instruction:
                config.system_instruction = system_instruction

            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=gemini_contents,
                config=config
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # Handle safety blocks
            if response.candidates and response.candidates[0].finish_reason == 'SAFETY':
                logger.warning(f"Gemini response blocked by safety filters")
                raise ValueError("Response blocked by safety filters")

            # Extract text, filtering out thinking content
            content = self._extract_response_text(response)

            return LLMResponse(
                content=content,
                tool_calls=[],
                provider=self.provider,
                model=self.model,
                usage={
                    'input_tokens': response.usage_metadata.prompt_token_count if response.usage_metadata else 0,
                    'output_tokens': response.usage_metadata.candidates_token_count if response.usage_metadata else 0,
                    'total_tokens': response.usage_metadata.total_token_count if response.usage_metadata else 0
                },
                latency_ms=latency_ms
            )

        except Exception as e:
            logger.error(f"Gemini generation error: {e}")
            raise

    async def generate_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> LLMResponse:
        """Generate response with tool calling"""
        start_time = time.time()

        params = self.sanitize_parameters(kwargs)
        system_instruction, gemini_contents = self._convert_messages(messages)
        gemini_tools = self._convert_tools(tools)

        try:
            config = types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens or self.capability.max_output_tokens,
                tools=gemini_tools,
                **params
            )

            # Add system instruction if present
            if system_instruction:
                config.system_instruction = system_instruction

            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=gemini_contents,
                config=config
            )

            latency_ms = int((time.time() - start_time) * 1000)

            # Normalize tool calls
            tool_calls = self.normalize_tool_calls(response)

            # Extract text, filtering out thinking content
            content = self._extract_response_text(response)

            return LLMResponse(
                content=content if content else None,
                tool_calls=tool_calls,
                provider=self.provider,
                model=self.model,
                usage={
                    'input_tokens': response.usage_metadata.prompt_token_count if response.usage_metadata else 0,
                    'output_tokens': response.usage_metadata.candidates_token_count if response.usage_metadata else 0,
                    'total_tokens': response.usage_metadata.total_token_count if response.usage_metadata else 0
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
        system_instruction, gemini_contents = self._convert_messages(messages)

        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_tokens or self.capability.max_output_tokens,
            **params
        )

        if system_instruction:
            config.system_instruction = system_instruction

        async for chunk in self.client.aio.models.generate_content_stream(
            model=self.model,
            contents=gemini_contents,
            config=config
        ):
            # Filter out thinking content from streaming chunks
            if chunk.candidates and chunk.candidates[0].content.parts:
                for part in chunk.candidates[0].content.parts:
                    # Skip thinking parts
                    if hasattr(part, 'thought') and part.thought:
                        continue
                    if hasattr(part, 'text') and part.text:
                        yield part.text

    def sanitize_parameters(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Remove unsupported parameters for Gemini"""
        # Gemini supports: top_p, top_k
        allowed = {'top_p', 'top_k'}
        return {k: v for k, v in params.items() if k in allowed}

    def _extract_response_text(self, response: Any) -> str:
        """
        Extract text from response, filtering out thinking/thought content.

        Gemini thinking models (2.5+, 3.x) may include 'thought' parts that contain
        internal reasoning. These should not be sent to users.
        """
        if not response.candidates or not response.candidates[0].content.parts:
            return ""

        text_parts = []
        for part in response.candidates[0].content.parts:
            # Skip thinking/thought parts - check for 'thought' attribute
            if hasattr(part, 'thought') and part.thought:
                logger.debug(f"Filtered out thinking content: {part.text[:100] if part.text else ''}...")
                continue

            # Skip parts with thought_signature (encrypted thinking state)
            if hasattr(part, 'thought_signature') and part.thought_signature:
                continue

            # Only include text parts (not function calls)
            if hasattr(part, 'text') and part.text:
                text_parts.append(part.text)

        return "".join(text_parts)

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

    def _convert_messages(self, messages: List[Dict[str, Any]]) -> Tuple[Optional[str], List[Content]]:
        """
        Convert OpenAI message format to Gemini format.

        Returns:
            Tuple of (system_instruction, contents_list)
        """
        system_instruction = None
        gemini_contents = []

        for msg in messages:
            role = msg.get('role', '')
            content = msg.get('content', '')

            # Extract system instruction (Gemini handles separately)
            if role == 'system':
                system_instruction = content
                continue

            # Convert user messages
            if role == 'user':
                gemini_contents.append(Content(
                    role='user',
                    parts=[Part(text=content if content else '')]
                ))

            # Convert assistant messages (may include tool calls)
            elif role == 'assistant':
                parts = []

                # Add text content if present
                if content:
                    parts.append(Part(text=content))

                # Add tool/function calls if present
                tool_calls = msg.get('tool_calls', [])
                for tc in tool_calls:
                    func = tc.get('function', {})
                    func_args = func.get('arguments', '{}')

                    # Parse arguments if string
                    if isinstance(func_args, str):
                        try:
                            func_args = json.loads(func_args)
                        except json.JSONDecodeError:
                            func_args = {}

                    parts.append(Part(
                        function_call=FunctionCall(
                            name=func.get('name', ''),
                            args=func_args
                        )
                    ))

                if parts:
                    gemini_contents.append(Content(role='model', parts=parts))

            # Convert tool results
            elif role == 'tool':
                tool_name = msg.get('name', msg.get('tool_call_id', 'unknown'))
                tool_content = content

                # Parse content if JSON string
                if isinstance(tool_content, str):
                    try:
                        tool_content = json.loads(tool_content)
                    except json.JSONDecodeError:
                        tool_content = {'result': tool_content}

                gemini_contents.append(Content(
                    role='user',  # Tool responses are from user perspective in Gemini
                    parts=[Part(
                        function_response=FunctionResponse(
                            name=tool_name,
                            response=tool_content if isinstance(tool_content, dict) else {'result': tool_content}
                        )
                    )]
                ))

        return system_instruction, gemini_contents

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
