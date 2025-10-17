"""
Backend orchestrator for the rule authoring assistant.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple, Union

from app.services.llm.base_adapter import LLMResponse, ToolCall
from app.services.rule_authoring.prompt_spec import (
    build_rule_authoring_prompt,
    get_rule_authoring_system_prompt,
    get_rule_authoring_tools,
    get_default_rule_authoring_llm_config,
    RuleAuthoringLLMConfig,
)


class RuleAuthoringError(Exception):
    """Base error for rule authoring orchestrator."""


class RuleAuthoringGuardrailError(RuleAuthoringError):
    """Raised when a guardrail (turns, cost, tool usage) is exceeded."""


ToolHandler = Callable[[Dict[str, Any]], Union[Dict[str, Any], Awaitable[Dict[str, Any]]]]


@dataclass
class RuleAuthoringSessionState:
    """Conversation state tracked across assistant turns."""

    messages: List[Dict[str, str]]
    clinic_id: Optional[str] = None
    tenant_id: Optional[str] = None
    administrator_name: Optional[str] = None
    tool_calls_used: int = 0
    turns: int = 0
    usd_spent: float = 0.0
    input_tokens_total: int = 0
    output_tokens_total: int = 0
    config: RuleAuthoringLLMConfig = field(default_factory=get_default_rule_authoring_llm_config)
    session_id: Optional[str] = None
    stored_messages: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class RuleAuthoringOrchestrator:
    """
    Coordinates the LLM chat flow for the rule authoring experience.

    Responsibilities:
    - Seed conversations using the standard system prompt.
    - Enforce guardrails on conversation length, tool usage, and budget.
    - Invoke tools through provided handlers and loop the assistant until a final response is produced.
    """

    def __init__(
        self,
        llm_factory,
        tool_handlers: Optional[Dict[str, ToolHandler]] = None,
        config: Optional[RuleAuthoringLLMConfig] = None,
    ):
        self.llm_factory = llm_factory
        self.tool_handlers = tool_handlers or {}
        self.default_config = config or get_default_rule_authoring_llm_config()

    async def start_session(
        self,
        clinic_name: str,
        administrator_name: str,
        *,
        clinic_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        starter_pack_enabled: bool = True,
        supplements: Optional[List[str]] = None,
        additional_context: Optional[str] = None,
        config: Optional[RuleAuthoringLLMConfig] = None
    ) -> RuleAuthoringSessionState:
        """ Initialise a new conversation state. """
        prompt_messages = build_rule_authoring_prompt(
            clinic_name=clinic_name,
            administrator_name=administrator_name,
            starter_pack_enabled=starter_pack_enabled,
            supplements=supplements,
            additional_context=additional_context,
        )

        return RuleAuthoringSessionState(
            messages=prompt_messages,
            clinic_id=clinic_id,
            tenant_id=tenant_id,
            administrator_name=administrator_name,
            config=config or self.default_config,
            stored_messages=len(prompt_messages)
        )

    async def process_user_message(
        self,
        state: RuleAuthoringSessionState,
        user_message: str,
        *,
        allow_tools: bool = True,
    ) -> str:
        """Process a user message and return the assistant's reply."""
        self._check_turn_guardrail(state)

        state.messages.append({"role": "user", "content": user_message})
        state.turns += 1

        response = await self._call_llm(
            state,
            use_tools=allow_tools and state.config.allow_tool_usage,
        )

        final_content = await self._handle_llm_response(state, response, allow_tools)
        return final_content

    async def _call_llm(
        self,
        state: RuleAuthoringSessionState,
        *,
        use_tools: bool,
    ) -> LLMResponse:
        messages = state.messages
        kwargs = {
            "temperature": state.config.temperature,
            "max_tokens": state.config.max_output_tokens,
        }

        if use_tools:
            tools = get_rule_authoring_tools()
            response = await self.llm_factory.generate_with_tools(
                messages=messages,
                tools=tools,
                model=state.config.reasoning_model,
                **kwargs,
            )
        else:
            response = await self.llm_factory.generate(
                messages=messages,
                model=state.config.reasoning_model,
                **kwargs,
            )

        await self._update_usage(state, response)
        return response

    async def _handle_llm_response(
        self,
        state: RuleAuthoringSessionState,
        response: LLMResponse,
        allow_tools: bool,
    ) -> str:
        state.messages.append({"role": "assistant", "content": response.content or ""})

        if response.tool_calls and allow_tools:
            final_content = await self._process_tool_calls(state, response.tool_calls)
            return final_content

        if response.content is None:
            raise RuleAuthoringError("Assistant returned no content and no tool calls.")

        return response.content

    async def _process_tool_calls(
        self,
        state: RuleAuthoringSessionState,
        tool_calls: List[ToolCall],
    ) -> str:
        results: List[str] = []
        for call in tool_calls:
            if state.tool_calls_used >= state.config.max_tool_calls:
                raise RuleAuthoringGuardrailError("Tool call limit reached for this session.")

            handler = self.tool_handlers.get(call.name)
            if handler is None:
                raise RuleAuthoringError(f"No tool handler registered for '{call.name}'.")

            payload = await self._run_tool_handler(handler, call.arguments)
            state.tool_calls_used += 1

            serialized = json.dumps(payload, default=str)
            state.messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": serialized,
                }
            )
            results.append(serialized)

        followup_response = await self._call_llm(state, use_tools=False)
        state.messages.append({"role": "assistant", "content": followup_response.content or ""})
        await self._update_usage(state, followup_response)

        if followup_response.content is None:
            raise RuleAuthoringError("Assistant returned empty content after tool execution.")

        return followup_response.content

    async def _run_tool_handler(
        self,
        handler: ToolHandler,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        result = handler(arguments)
        if asyncio.iscoroutine(result):
            result = await result
        if not isinstance(result, dict):
            raise RuleAuthoringError("Tool handler must return a dictionary payload.")
        return result

    async def _update_usage(self, state: RuleAuthoringSessionState, response: LLMResponse) -> None:
        usage = response.usage or {}
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        state.input_tokens_total += input_tokens
        state.output_tokens_total += output_tokens

        capability = await self.llm_factory.capability_matrix.load_model(response.model)
        input_cost = (input_tokens / 1_000_000) * capability.input_price_per_1m
        output_cost = (output_tokens / 1_000_000) * capability.output_price_per_1m
        total_cost = input_cost + output_cost

        state.usd_spent += total_cost
        if state.usd_spent > state.config.budget_usd_per_session:
            raise RuleAuthoringGuardrailError("Session budget exceeded.")

    def _check_turn_guardrail(self, state: RuleAuthoringSessionState) -> None:
        if state.turns >= state.config.max_conversation_turns:
            raise RuleAuthoringGuardrailError("Conversation turn limit reached.")
