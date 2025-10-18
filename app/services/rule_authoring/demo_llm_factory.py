"""Simple in-process LLM factory used for demo/testing of the rule authoring API.

The production implementation relies on the shared `LLMFactory`, but that requires
external credentials and network access. This lightweight variant keeps the API
usable in local environments by producing deterministic responses and zero-cost
usage metrics.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, List, Optional

from app.services.llm.base_adapter import LLMResponse


class DemoLLMFactory:
    """Minimal factory that returns canned responses for the orchestrator."""

    def __init__(self) -> None:
        capability = SimpleNamespace(input_price_per_1m=0.0, output_price_per_1m=0.0)

        async def _load_model_async(model_name: str):  # pragma: no cover - trivial
            return capability

        self.capability_matrix = SimpleNamespace(load_model=_load_model_async)
        self._default_model = "demo-rule-writer"

    async def generate(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str] = None,
        **_: Any,
    ) -> LLMResponse:
        return self._build_response(messages, model)

    async def generate_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: List[Dict[str, Any]],
        model: Optional[str] = None,
        **_: Any,
    ) -> LLMResponse:
        # The demo implementation does not attempt tool calls. It simply returns a
        # conversational response so the orchestrator can proceed without errors.
        return self._build_response(messages, model, include_tool_hint=bool(tools))

    def _build_response(
        self,
        messages: List[Dict[str, str]],
        model: Optional[str],
        include_tool_hint: bool = False,
    ) -> LLMResponse:
        last_user_message = next(
            (msg["content"] for msg in reversed(messages) if msg.get("role") == "user"),
            "",
        )
        hint = " (tools available)" if include_tool_hint else ""
        content = (
            "Demo response" + hint + ": " + last_user_message
            if last_user_message
            else "Demo response: share more details about the scheduling policy you have in mind."
        )

        return LLMResponse(
            content=content,
            tool_calls=[],
            provider="demo",
            model=model or self._default_model,
            usage={"input_tokens": 200, "output_tokens": 320, "total_tokens": 520},
            latency_ms=45,
        )
