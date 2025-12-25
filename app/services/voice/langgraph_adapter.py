"""
LangGraph Voice Adapter - Bridges LangGraph to LiveKit Agents.

This module provides a unified interface for voice services to use the same
LangGraph-based orchestration as text channels. It uses astream_events for
real token streaming, which is critical for voice latency (TTFT).

Usage:
    # For direct streaming (e.g., from SSE endpoint)
    adapter = LangGraphVoiceAdapter(clinic_id="clinic123")
    async for chunk in adapter.stream_response(message, session_id, metadata):
        yield chunk

    # For LiveKit integration (when livekit-agents is installed)
    from app.services.voice.livekit_llm import LangGraphLLM
    llm = LangGraphLLM(clinic_id="clinic123")
    # Use as LiveKit llm.LLM plugin

Reference: https://docs.livekit.io/agents/models/llm/plugins/langchain/
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Dict, Any, Optional, List
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


@dataclass
class StreamingResponse:
    """
    Streaming response chunk from LangGraph.

    Attributes:
        content: Text content of this chunk
        is_final: True if this is the last chunk
        node_name: Which graph node generated this chunk (for debugging)
        metadata: Additional metadata
    """
    content: str
    is_final: bool = False
    node_name: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class LangGraphVoiceAdapter:
    """
    Adapts HealthcareLangGraph for voice services.

    Key features:
    - Real token streaming via astream_events (critical for voice TTFT)
    - Session-scoped thread_id for state continuity
    - Fallback to full response if streaming fails
    - Metrics for latency tracking

    Usage:
        adapter = LangGraphVoiceAdapter(clinic_id="clinic123")

        # Stream response tokens
        async for chunk in adapter.stream_response(message, session_id, metadata):
            send_to_tts(chunk.content)
    """

    def __init__(
        self,
        clinic_id: str,
        supabase_client: Optional[Any] = None,
        agent_config: Optional[Dict[str, Any]] = None,
    ):
        """
        Initialize the voice adapter.

        Args:
            clinic_id: Clinic identifier for context
            supabase_client: Optional Supabase client (lazy-loaded if not provided)
            agent_config: Optional agent configuration
        """
        self.clinic_id = clinic_id
        self._supabase_client = supabase_client
        self._agent_config = agent_config
        self._graph = None  # Lazy-loaded

    async def _get_graph(self):
        """Lazy-load the HealthcareLangGraph instance."""
        if self._graph is None:
            from app.services.orchestrator.templates.healthcare_template import HealthcareLangGraph

            # Get supabase client if not provided
            if self._supabase_client is None:
                from app.database import get_supabase
                self._supabase_client = await get_supabase()

            self._graph = HealthcareLangGraph(
                clinic_id=self.clinic_id,
                supabase_client=self._supabase_client,
                agent_config=self._agent_config,
            )
            logger.info(f"[voice-adapter] Initialized HealthcareLangGraph for clinic {self.clinic_id}")

        return self._graph

    def _make_thread_id(self, patient_id: str, session_id: str) -> str:
        """Generate session-scoped thread ID."""
        from app.services.orchestrator.thread_ids import make_thread_id
        return make_thread_id(self.clinic_id, patient_id, session_id)

    def _make_checkpoint_ns(self) -> str:
        """Generate checkpoint namespace for tenant isolation."""
        from app.services.orchestrator.thread_ids import make_checkpoint_ns
        return make_checkpoint_ns(self.clinic_id)

    async def stream_response(
        self,
        message: str,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        patient_id: Optional[str] = None,
    ) -> AsyncIterator[StreamingResponse]:
        """
        Stream LangGraph response tokens.

        Uses astream_events to yield tokens from the generate_response node
        as soon as they're generated - critical for voice TTFT.

        Args:
            message: User message text
            session_id: Session identifier
            metadata: Additional metadata (participant info, etc.)
            patient_id: Optional patient ID (falls back to extracting from metadata)

        Yields:
            StreamingResponse chunks with token content
        """
        metadata = metadata or {}
        start_time = datetime.now(timezone.utc)

        # Resolve patient_id
        if patient_id is None:
            patient_id = (
                metadata.get("participant_id") or
                metadata.get("patient_id") or
                metadata.get("from_phone", "").replace("+", "") or
                session_id
            )

        # Build thread_id for state continuity
        thread_id = self._make_thread_id(patient_id, session_id)
        checkpoint_ns = self._make_checkpoint_ns()

        logger.info(
            f"[voice-adapter] Processing message for thread {thread_id[:30]}..."
        )

        # Get graph instance
        graph = await self._get_graph()

        # Build graph input state
        graph_input = {
            "message": message,
            "session_id": session_id,
            "metadata": {
                "channel": "voice",
                "thread_id": thread_id,
                "clinic_id": self.clinic_id,
                "patient_id": patient_id,
                **metadata,
            },
            # Initialize required state fields
            "context": {},
            "contains_phi": False,
            "is_emergency": False,
            "detected_language": "es",  # Default, will be detected
            "fast_path": False,
            "context_hydrated": False,
        }

        config = {
            "configurable": {
                "thread_id": thread_id,
                "checkpoint_ns": checkpoint_ns,
            }
        }

        tokens_yielded = 0
        first_token_time = None

        try:
            # Stream events from graph execution
            # This is the key for voice latency - we get tokens as they're generated
            async for event in graph.compiled_graph.astream_events(
                graph_input,
                config=config,
                version="v2",  # Use v2 for better event structure
            ):
                # Look for streaming tokens from the response generation node
                if event["event"] == "on_chat_model_stream":
                    node_name = event.get("metadata", {}).get("langgraph_node", "")

                    # Only yield tokens from response-generating nodes
                    if node_name in ("generate_response", "simple_answer", "process"):
                        chunk_data = event.get("data", {})
                        chunk = chunk_data.get("chunk")

                        if chunk and hasattr(chunk, "content") and chunk.content:
                            if first_token_time is None:
                                first_token_time = datetime.now(timezone.utc)
                                ttft_ms = (first_token_time - start_time).total_seconds() * 1000
                                logger.info(f"[voice-adapter] TTFT: {ttft_ms:.0f}ms")

                            tokens_yielded += 1
                            yield StreamingResponse(
                                content=chunk.content,
                                is_final=False,
                                node_name=node_name,
                            )

                # Also check for final response in state updates
                elif event["event"] == "on_chain_end":
                    # Graph completed - check if we got any tokens
                    pass

            # If we didn't stream any tokens, fall back to getting full response
            if tokens_yielded == 0:
                logger.warning("[voice-adapter] No streaming tokens - using fallback")
                result = await self.get_response(message, session_id, metadata, patient_id)
                yield StreamingResponse(
                    content=result["response"],
                    is_final=True,
                    node_name="fallback",
                    metadata=result,
                )
            else:
                # Send final chunk marker
                total_time_ms = (datetime.now(timezone.utc) - start_time).total_seconds() * 1000
                yield StreamingResponse(
                    content="",
                    is_final=True,
                    metadata={
                        "tokens_yielded": tokens_yielded,
                        "total_time_ms": total_time_ms,
                        "ttft_ms": (first_token_time - start_time).total_seconds() * 1000 if first_token_time else None,
                    },
                )

                logger.info(
                    f"[voice-adapter] Completed: {tokens_yielded} tokens in {total_time_ms:.0f}ms"
                )

        except Exception as e:
            logger.error(f"[voice-adapter] Streaming failed: {e}", exc_info=True)
            # Fall back to full response
            try:
                result = await self.get_response(message, session_id, metadata, patient_id)
                yield StreamingResponse(
                    content=result["response"],
                    is_final=True,
                    node_name="error_fallback",
                    metadata={"error": str(e)},
                )
            except Exception as fallback_error:
                yield StreamingResponse(
                    content="I apologize, but I'm having trouble processing your request. Please try again.",
                    is_final=True,
                    node_name="error",
                    metadata={"error": str(fallback_error)},
                )

    async def get_response(
        self,
        message: str,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None,
        patient_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Get full response from LangGraph (non-streaming).

        Use this for:
        - Fallback when streaming fails
        - Text channels that don't need streaming
        - Testing

        Args:
            message: User message text
            session_id: Session identifier
            metadata: Additional metadata
            patient_id: Optional patient ID

        Returns:
            Dict with response, intent, metadata, etc.
        """
        metadata = metadata or {}

        # Resolve patient_id
        if patient_id is None:
            patient_id = (
                metadata.get("participant_id") or
                metadata.get("patient_id") or
                metadata.get("from_phone", "").replace("+", "") or
                session_id
            )

        # Build context for graph
        context = {
            "clinic_profile": metadata.get("clinic_profile", {}),
            "patient_profile": metadata.get("patient_profile", {}),
        }

        # Get graph instance
        graph = await self._get_graph()

        # Process through graph
        result = await graph.process(
            message=message,
            session_id=session_id,
            metadata={
                "channel": "voice",
                "clinic_id": self.clinic_id,
                "patient_id": patient_id,
                **metadata,
            },
            patient_id=patient_id,
            context=context,
        )

        return result

    async def close(self):
        """Clean up resources."""
        if self._graph is not None:
            # Close any graph resources if needed
            self._graph = None
            logger.info(f"[voice-adapter] Closed adapter for clinic {self.clinic_id}")


# Factory function for easier instantiation
async def create_voice_adapter(
    clinic_id: str,
    supabase_client: Optional[Any] = None,
) -> LangGraphVoiceAdapter:
    """
    Create a voice adapter for a clinic.

    Args:
        clinic_id: Clinic identifier
        supabase_client: Optional Supabase client

    Returns:
        Configured LangGraphVoiceAdapter
    """
    adapter = LangGraphVoiceAdapter(
        clinic_id=clinic_id,
        supabase_client=supabase_client,
    )
    # Pre-initialize the graph
    await adapter._get_graph()
    return adapter
