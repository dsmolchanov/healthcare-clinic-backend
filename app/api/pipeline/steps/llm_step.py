"""
LLMGenerationStep - Generate AI response using LLM with tools.

Extracted from _generate_response() method (lines 845-1487).

Phase 2A of the Agentic Flow Architecture Refactor.
Phase 2B: Updated to use PromptComposer for modular prompts.
"""

import os
import re
import json
import asyncio
import time
import logging
from datetime import datetime, timedelta
from typing import Tuple, Dict, Any, List, Optional

from ..base import PipelineStep
from ..context import PipelineContext
from app.domain.preferences.narrowing import NarrowingAction, NarrowingInstruction
from app.prompts import (
    PromptComposer,
    build_doctors_text,
    build_profile_section,
    build_conversation_summary,
    build_constraints_section,
)

logger = logging.getLogger(__name__)


# Question type to template mapping (LLM localizes based on user language)
# Kept here for backward compatibility - also exported from app.prompts.components
QUESTION_TEMPLATES = {
    "ask_for_service": "Ask what service the user needs (e.g., cleaning, checkup, whitening)",
    "ask_for_time": "Ask what day and time works best for the user",
    "ask_for_doctor": "Ask if user prefers {doctor_names} or first available",
    "ask_time_with_doctor": "Ask when user would like to see {doctor_name}",
    "ask_time_with_service": "Ask when user would like their {service_name} appointment",
    "ask_today_or_tomorrow": "Ask if user prefers today or tomorrow (urgent case)",
    "suggest_consultation": "Explain no specialists for {service_name}, suggest general consultation",
    "ask_first_available": "Ask if user prefers {doctor_names} or first availability",
}


class LLMGenerationStep(PipelineStep):
    """
    Generate AI response using LLM with tool calling.

    Responsibilities:
    1. Build system prompt with clinic context, constraints, profile
    2. Execute LLM with tool calling support
    3. Handle multi-turn tool execution
    4. Clean response and detect language
    """

    def __init__(
        self,
        llm_factory_getter=None,
        tool_executor=None,
        constraints_manager=None,
        language_service=None,
        prompt_composer=None
    ):
        """
        Initialize with LLM dependencies.

        Args:
            llm_factory_getter: Async function to get LLMFactory
            tool_executor: ToolExecutor for executing tool calls
            constraints_manager: ConstraintsManager for persisting tool call context
            language_service: LanguageService for response language detection
            prompt_composer: PromptComposer for building system prompts (optional)
        """
        self._get_llm_factory = llm_factory_getter
        self._tool_executor = tool_executor
        self._constraints_manager = constraints_manager
        self._language_service = language_service
        self._prompt_composer = prompt_composer or PromptComposer()

    @property
    def name(self) -> str:
        return "llm_generation"

    async def execute(self, ctx: PipelineContext) -> Tuple[PipelineContext, bool]:
        """
        Execute LLM generation step.

        Sets on context:
        - response
        - detected_language
        - llm_metrics
        """
        llm_start = time.time()

        try:
            # Build system prompt
            system_prompt = self._build_system_prompt(ctx)

            # Build messages
            messages = self._build_messages(system_prompt, ctx)

            # Get tool schemas
            from app.api.tool_schemas import get_tool_schemas
            from app.tools import conversation_history_tools

            # Set context for history tools
            conversation_history_tools.set_context(
                phone_number=ctx.from_phone,
                clinic_id=ctx.effective_clinic_id
            )

            tool_schemas = get_tool_schemas(ctx.effective_clinic_id)

            # Add conversation history tools
            tool_schemas.append(conversation_history_tools.get_previous_conversations_summary.tool_schema)
            tool_schemas.append(conversation_history_tools.search_detailed_conversation_history.tool_schema)

            logger.info(f"Loaded {len(tool_schemas)} tool schemas for clinic {ctx.effective_clinic_id}")

            # Execute LLM with tools
            response_text = await self._execute_llm_with_tools(
                ctx=ctx,
                messages=messages,
                tool_schemas=tool_schemas,
                llm_start=llm_start
            )

            # Clean response
            ctx.response = self._clean_llm_response(response_text, ctx.message)

            # Detect response language
            if self._language_service:
                ctx.detected_language = self._language_service.detect_sync(ctx.response)

            logger.info(f"💬 LLM response generated in {(time.time() - llm_start) * 1000:.0f}ms")

        except asyncio.TimeoutError:
            logger.error("LLM call exceeded timeout, using fallback")
            ctx.response = self._get_timeout_fallback(ctx)
            ctx.llm_metrics['error_occurred'] = True
            ctx.llm_metrics['error_message'] = 'LLM timeout'

        except Exception as e:
            logger.error(f"Error generating AI response: {e}", exc_info=True)
            ctx.response = self._get_error_fallback(ctx.detected_language)
            ctx.llm_metrics['error_occurred'] = True
            ctx.llm_metrics['error_message'] = str(e)

        return ctx, True

    def _build_system_prompt(self, ctx: PipelineContext) -> str:
        """
        Build the system prompt with clinic context, constraints, profile.

        Phase 2B: Delegates to PromptComposer for modular prompt composition.
        """
        return self._prompt_composer.compose(ctx)

    def _build_doctors_text(self, doctors_list: List) -> str:
        """
        Build doctors section for prompt.

        Phase 2B: Delegates to build_doctors_text from prompts.components.
        """
        return build_doctors_text(doctors_list)

    # NOTE: The following methods are kept for backward compatibility but are now
    # deprecated. The PromptComposer handles all prompt building internally.
    # These delegate to the functions in app.prompts.components.

    def _build_profile_section(self, ctx: PipelineContext) -> str:
        """
        Build patient profile section for prompt.

        DEPRECATED: Use PromptComposer.compose() instead.
        """
        return build_profile_section(ctx.profile, ctx.conversation_state)

    def _build_conversation_summary(self, session_messages: List) -> str:
        """
        Build conversation summary from history.

        DEPRECATED: Use PromptComposer.compose() instead.
        """
        return build_conversation_summary(session_messages)

    def _build_constraints_section(self, constraints) -> str:
        """
        Build constraints section for system prompt.

        DEPRECATED: Use PromptComposer.compose() instead.
        """
        return build_constraints_section(constraints)

    def _build_messages(self, system_prompt: str, ctx: PipelineContext) -> List[Dict]:
        """Build messages list for LLM."""
        messages = [{"role": "system", "content": system_prompt}]

        # Add conversation history
        for msg in ctx.session_messages[-12:]:
            messages.append({
                "role": msg['role'],
                "content": msg['content']
            })

        # Add current message
        messages.append({"role": "user", "content": ctx.message})

        return messages

    async def _execute_llm_with_tools(
        self,
        ctx: PipelineContext,
        messages: List[Dict],
        tool_schemas: List[Dict],
        llm_start: float
    ) -> str:
        """Execute LLM with multi-turn tool calling."""
        try:
            factory = await self._get_llm_factory()

            llm_response = await asyncio.wait_for(
                factory.generate_with_tools(
                    messages=messages,
                    tools=tool_schemas,
                    model=None,
                    temperature=1.0,
                    max_tokens=300
                ),
                timeout=20.0
            )

            # Multi-turn tool execution loop
            max_tool_turns = 5
            current_turn = 0
            prior_tool_results = {}

            # Reset tool state gate counters
            if self._tool_executor:
                self._tool_executor.state_gate.reset_turn_counters()

            current_flow_state = "idle"

            while current_turn < max_tool_turns:
                current_turn += 1

                if llm_response.tool_calls and len(llm_response.tool_calls) > 0:
                    logger.info(f"LLM requesting {len(llm_response.tool_calls)} tool call(s) (Turn {current_turn})")

                    # Get business_hours from already-hydrated clinic_profile (no extra DB call)
                    clinic_profile = ctx.clinic_profile or {}
                    business_hours = clinic_profile.get('business_hours') or clinic_profile.get('hours') or {}

                    tool_context = {
                        'clinic_id': ctx.effective_clinic_id,
                        'phone_number': ctx.from_phone,
                        'session_history': ctx.session_messages,
                        'business_hours': business_hours,  # From warmup, no extra DB fetch
                        'supabase_client': None,  # Will be set by executor
                        'calendar_calls_made': 0,
                        'max_calendar_calls': 10
                    }

                    tool_results = []
                    for tool_call in llm_response.tool_calls:
                        tool_name = tool_call.name
                        tool_args = tool_call.arguments if isinstance(tool_call.arguments, dict) else json.loads(tool_call.arguments)

                        if self._tool_executor:
                            result, prior_tool_results = await self._tool_executor.execute(
                                tool_call_id=tool_call.id,
                                tool_name=tool_name,
                                tool_args=tool_args,
                                context=tool_context,
                                constraints=ctx.constraints,
                                current_state=current_flow_state,
                                tool_schemas=tool_schemas,
                                prior_tool_results=prior_tool_results
                            )
                            tool_results.append(result)

                            # Persist context from tool calls
                            if self._constraints_manager and tool_name in ['check_availability', 'book_appointment']:
                                new_service = tool_args.get('service_name')
                                new_doctor_id = tool_args.get('doctor_id')
                                if new_service or new_doctor_id:
                                    await self._constraints_manager.update_constraints(
                                        ctx.session_id,
                                        desired_service=new_service,
                                        desired_doctor=new_doctor_id
                                    )

                    # Add tool results to messages (include metadata for Gemini thought_signature)
                    messages.append({
                        "role": "assistant",
                        "content": llm_response.content or "",
                        "tool_calls": [
                            {
                                "id": tc.id,
                                "type": "function",
                                "function": {
                                    "name": tc.name,
                                    "arguments": json.dumps(tc.arguments) if isinstance(tc.arguments, dict) else tc.arguments
                                },
                                "metadata": tc.metadata  # Preserve provider-specific metadata (e.g., Gemini thought_signature)
                            }
                            for tc in llm_response.tool_calls
                        ]
                    })

                    for tool_result in tool_results:
                        messages.append(tool_result)

                    # Get next response
                    llm_response = await factory.generate_with_tools(
                        messages=messages,
                        tools=tool_schemas,
                        model=None,
                        temperature=0.7,
                        max_tokens=300
                    )

                    if not llm_response.tool_calls:
                        break
                else:
                    break

            if current_turn >= max_tool_turns:
                logger.warning("Max tool turns reached")

            # Calculate metrics
            llm_latency_ms = int((time.time() - llm_start) * 1000)
            ctx.llm_metrics = {
                'llm_provider': llm_response.provider,
                'llm_model': llm_response.model,
                'llm_tokens_input': llm_response.usage.get('input_tokens', 0),
                'llm_tokens_output': llm_response.usage.get('output_tokens', 0),
                'llm_latency_ms': llm_latency_ms,
                'llm_cost_usd': self._calculate_cost(llm_response)
            }

            return llm_response.content or ""

        except (ValueError, RuntimeError) as factory_error:
            logger.error(f"LLM Factory not available: {factory_error}")
            raise RuntimeError(f"LLM generation failed: {factory_error}")

    def _calculate_cost(self, llm_response) -> float:
        """Calculate LLM cost from response."""
        try:
            pricing_map = {
                'glm': {'input': 0.60, 'output': 2.20},
                'google': {'input': 0.10, 'output': 0.40},
                'openai': {'input': 0.05, 'output': 0.40},
            }

            provider = llm_response.provider
            if provider not in pricing_map:
                return 0.0

            pricing = pricing_map[provider]
            input_tokens = llm_response.usage.get('input_tokens', 0)
            output_tokens = llm_response.usage.get('output_tokens', 0)

            input_cost = (input_tokens / 1_000_000) * pricing['input']
            output_cost = (output_tokens / 1_000_000) * pricing['output']

            return round(input_cost + output_cost, 6)
        except Exception:
            return 0.0

    def _clean_llm_response(self, response: str, user_message: str = "") -> str:
        """Remove <think> tags and reasoning from response."""
        if not response:
            return self._get_error_fallback('en')

        # Remove complete <think>...</think> blocks
        while re.search(r'<think>.*?</think>', response, flags=re.DOTALL):
            response = re.sub(r'<think>.*?</think>', '', response, flags=re.DOTALL)

        # Remove partial tags
        if '</think>' in response:
            parts = response.split('</think>')
            response = parts[-1]

        if '<think>' in response:
            response = response.split('<think>')[0]

        # Clean up whitespace
        response = re.sub(r'\n{3,}', '\n\n', response)
        response = response.strip()

        if not response:
            return self._get_error_fallback('en')

        return response

    def _get_timeout_fallback(self, ctx: PipelineContext) -> str:
        """Get fallback response for timeout."""
        doctors_text = self._build_doctors_text(ctx.clinic_doctors)
        user_lower = ctx.message.lower()

        is_doctor_query = any(kw in user_lower for kw in ['doctor', 'доктор', 'врач', 'médico'])

        if is_doctor_query and doctors_text:
            fallbacks = {
                'en': f"We have the following doctors:\n\n{doctors_text}",
                'ru': f"У нас работают следующие врачи:\n\n{doctors_text}",
                'es': f"Tenemos los siguientes médicos:\n\n{doctors_text}",
            }
            return fallbacks.get(ctx.detected_language, fallbacks['en'])

        return self._get_error_fallback(ctx.detected_language)

    def _get_error_fallback(self, language: str) -> str:
        """Get generic error fallback message."""
        fallbacks = {
            'en': "I understand. How can I help you today?",
            'ru': "Понимаю. Чем могу помочь?",
            'es': "Entiendo. ¿En qué puedo ayudarte?",
            'he': "אני מבין. במה אוכל לעזור?",
            'pt': "Entendo. Como posso ajudar?"
        }
        return fallbacks.get(language, fallbacks['en'])
