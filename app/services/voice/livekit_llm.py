"""
LiveKit LLM Adapter - Implements LiveKit Agents LLM interface.

This module wraps LangGraphVoiceAdapter as a LiveKit LLM plugin, enabling
voice agents to use the same LangGraph orchestration as text channels.

Usage in voice-worker:
    from app.services.voice.livekit_llm import LangGraphLLM

    async def entrypoint(ctx: JobContext):
        llm = LangGraphLLM(clinic_id="clinic123")
        agent = VoicePipelineAgent(
            llm=llm,
            stt=stt,
            tts=tts,
            ...
        )

IMPORTANT:
- This module requires livekit-agents to be installed
- Falls back gracefully if livekit is not available
- Uses astream_events for real token streaming (critical for voice TTFT)

Reference: https://docs.livekit.io/agents/v0-migration/python/
"""

import asyncio
import logging
from typing import Optional, Dict, Any, Literal, Union

logger = logging.getLogger(__name__)

# Try to import LiveKit - graceful fallback if not available
try:
    from livekit.agents import llm
    from livekit.agents.llm import (
        ChatContext,
        LLMStream,
        ChatChunk,
        Choice,
        ChoiceDelta,
        FunctionContext,
        APIConnectOptions,
        ToolChoice,
    )
    LIVEKIT_AVAILABLE = True
except ImportError:
    LIVEKIT_AVAILABLE = False
    logger.info("[livekit-llm] livekit-agents not installed - LangGraphLLM not available")


if LIVEKIT_AVAILABLE:

    class LangGraphLLM(llm.LLM):
        """
        LangGraph as a LiveKit LLM plugin.

        Enables unified voice + text on the same LangGraph:
        - Voice calls use this LLM adapter
        - Text channels call LangGraph directly
        - Same graph, same tools, same orchestration

        Key features:
        - Real token streaming via astream_events (critical for voice TTFT)
        - Session-scoped thread_id for state continuity
        - Proper LiveKit 1.x API signature
        """

        def __init__(
            self,
            clinic_id: str,
            supabase_client: Optional[Any] = None,
            agent_config: Optional[Dict[str, Any]] = None,
        ):
            """
            Initialize LangGraph LLM adapter.

            Args:
                clinic_id: Clinic identifier
                supabase_client: Optional Supabase client
                agent_config: Optional agent configuration
            """
            super().__init__()
            self.clinic_id = clinic_id
            self._supabase_client = supabase_client
            self._agent_config = agent_config
            self._adapter = None  # Lazy-loaded

        async def _get_adapter(self):
            """Lazy-load the LangGraphVoiceAdapter."""
            if self._adapter is None:
                from .langgraph_adapter import LangGraphVoiceAdapter
                self._adapter = LangGraphVoiceAdapter(
                    clinic_id=self.clinic_id,
                    supabase_client=self._supabase_client,
                    agent_config=self._agent_config,
                )
            return self._adapter

        def chat(
            self,
            *,
            chat_ctx: ChatContext,
            conn_options: APIConnectOptions = llm.DEFAULT_API_CONNECT_OPTIONS,
            fnc_ctx: Optional[FunctionContext] = None,
            temperature: Optional[float] = None,
            n: Optional[int] = None,
            parallel_tool_calls: Optional[bool] = None,
            tool_choice: Optional[Union[ToolChoice, Literal["auto", "required", "none"]]] = None,
        ) -> LLMStream:
            """
            Process chat through LangGraph with real token streaming.

            NOTE: LangGraph handles tools internally, so we ignore:
            - fnc_ctx (function context)
            - parallel_tool_calls
            - tool_choice

            The graph's supervisor routes to appropriate agents/tools.

            Args:
                chat_ctx: LiveKit chat context with message history
                conn_options: Connection options (timeouts, etc.)
                fnc_ctx: Function context (ignored - graph handles tools)
                temperature: Temperature (passed to graph's LLM calls)
                n: Number of completions (ignored - always 1)
                parallel_tool_calls: Ignored - graph handles this
                tool_choice: Ignored - graph supervisor decides

            Returns:
                LLMStream that yields tokens as they're generated
            """
            # Extract participant info from chat context
            metadata = getattr(chat_ctx, "metadata", {}) or {}
            participant_id = metadata.get("participant_id", "unknown")
            session_id = metadata.get("session_id", participant_id)

            # Get latest user message
            user_message = ""
            if chat_ctx.messages:
                last_msg = chat_ctx.messages[-1]
                if hasattr(last_msg, "content"):
                    content = last_msg.content
                    if isinstance(content, list) and content:
                        user_message = str(content[0])
                    elif isinstance(content, str):
                        user_message = content

            logger.info(f"[livekit-llm] Processing: {user_message[:50]}...")

            # Create stream that will run graph in background
            return LangGraphLLMStream(
                llm=self,
                chat_ctx=chat_ctx,
                fnc_ctx=fnc_ctx,
                conn_options=conn_options,
                user_message=user_message,
                session_id=session_id,
                metadata=metadata,
            )


    class LangGraphLLMStream(LLMStream):
        """
        Stream wrapper for LangGraph response.

        Uses astream_events to pipe tokens to TTS as soon as generate_response
        node starts producing them - critical for voice latency.

        Implements LiveKit's LLMStream protocol for compatibility with
        VoicePipelineAgent.
        """

        def __init__(
            self,
            *,
            llm: LangGraphLLM,
            chat_ctx: ChatContext,
            fnc_ctx: Optional[FunctionContext],
            conn_options: APIConnectOptions,
            user_message: str,
            session_id: str,
            metadata: Dict[str, Any],
        ):
            super().__init__(llm, chat_ctx, fnc_ctx, conn_options)
            self._llm = llm
            self._user_message = user_message
            self._session_id = session_id
            self._metadata = metadata

        async def _run(self) -> None:
            """
            Execute graph with real token streaming.

            Streams tokens from the response nodes as they're generated,
            rather than waiting for full graph execution.

            This is critical for voice Time-to-First-Token (TTFT).
            """
            try:
                adapter = await self._llm._get_adapter()

                # Stream tokens from LangGraph
                async for chunk in adapter.stream_response(
                    message=self._user_message,
                    session_id=self._session_id,
                    metadata=self._metadata,
                ):
                    if chunk.content and not chunk.is_final:
                        # Push token to LiveKit TTS immediately
                        self._event_ch.send_nowait(
                            ChatChunk(
                                choices=[
                                    Choice(
                                        delta=ChoiceDelta(
                                            role="assistant",
                                            content=chunk.content,
                                        ),
                                        index=0,
                                    )
                                ]
                            )
                        )
                    elif chunk.is_final:
                        # Log completion metrics
                        if chunk.metadata:
                            logger.info(
                                f"[livekit-llm] Complete: "
                                f"tokens={chunk.metadata.get('tokens_yielded', 0)}, "
                                f"ttft={chunk.metadata.get('ttft_ms', 'N/A')}ms, "
                                f"total={chunk.metadata.get('total_time_ms', 'N/A')}ms"
                            )

            except Exception as e:
                logger.error(f"[livekit-llm] Streaming failed: {e}", exc_info=True)

                # Fallback: get full response and send as single chunk
                try:
                    adapter = await self._llm._get_adapter()
                    result = await adapter.get_response(
                        message=self._user_message,
                        session_id=self._session_id,
                        metadata=self._metadata,
                    )

                    response = result.get("response", "I apologize, but I'm having trouble. Please try again.")
                    self._event_ch.send_nowait(
                        ChatChunk(
                            choices=[
                                Choice(
                                    delta=ChoiceDelta(
                                        role="assistant",
                                        content=response,
                                    ),
                                    index=0,
                                )
                            ]
                        )
                    )
                except Exception as fallback_error:
                    logger.error(f"[livekit-llm] Fallback also failed: {fallback_error}")
                    self._event_ch.send_nowait(
                        ChatChunk(
                            choices=[
                                Choice(
                                    delta=ChoiceDelta(
                                        role="assistant",
                                        content="I apologize, but I'm experiencing technical difficulties.",
                                    ),
                                    index=0,
                                )
                            ]
                        )
                    )


else:
    # Fallback when livekit-agents is not installed
    class LangGraphLLM:
        """Placeholder when livekit-agents is not installed."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "livekit-agents is not installed. "
                "Install it with: pip install livekit-agents"
            )

    class LangGraphLLMStream:
        """Placeholder when livekit-agents is not installed."""

        def __init__(self, *args, **kwargs):
            raise ImportError(
                "livekit-agents is not installed. "
                "Install it with: pip install livekit-agents"
            )
